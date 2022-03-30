import os
import random


def main():
    random.seed(0)  # random seeds

    # input: annotation xml files
<<<<<<< HEAD
    files_path = 'C://Users\\VelocityUser\\Documents\\D2K TDS A\\6_class_combine\\images'
    # files_path = "/Users/maojietang/Downloads/VOCdevkit/VOC2012/Annotations"
    # assert os.path.exists(files_path), "path: '{}' does not exist.".format(files_path)
=======
    files_path = "/Users/maojietang/Downloads/VOCdevkit/VOC2012/Annotations"
    # files_path = "/Users/maojietang/Downloads/VOCdevkit/VOC2012/Annotations"
    assert os.path.exists(files_path), "path: '{}' does not exist.".format(files_path)
>>>>>>> e55d678011589736c57c1965d915317b7a449b1f

    val_rate = 0.1

    files_name = sorted([file.split(".")[0] for file in os.listdir(files_path) if file.split(".")[0] != ''])
    files_num = len(files_name)
    val_index = random.sample(range(0, files_num), k=int(files_num*val_rate))
    train_files = []
    val_files = []
    for index, file_name in enumerate(files_name):
        if index in val_index:
            val_files.append(file_name)
        else:
            train_files.append(file_name)

    try:

        train_f = open("train.txt", "x")
        eval_f = open("val.txt", "x")
        train_f.write("\n".join(train_files))
        eval_f.write("\n".join(val_files))
    except FileExistsError as e:
        print(e)
        exit(1)


if __name__ == '__main__':
    main()
    print('Data processing completed')
